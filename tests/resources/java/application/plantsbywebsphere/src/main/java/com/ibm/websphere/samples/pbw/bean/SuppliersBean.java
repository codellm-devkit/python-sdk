//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2004,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.bean;

import java.io.Serializable;
import java.util.Collection;
import java.util.Iterator;

import javax.enterprise.context.Dependent;
import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;
import javax.persistence.Query;

import com.ibm.websphere.samples.pbw.jpa.Supplier;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Bean implementation class for Enterprise Bean: Suppliers
 */
@Dependent
public class SuppliersBean implements Serializable {

	@PersistenceContext(unitName = "PBW")
	EntityManager em;

	/**
	 * @param supplierID
	 * @param name
	 * @param street
	 * @param city
	 * @param state
	 * @param zip
	 * @param phone
	 * @param url
	 */
	public void createSupplier(String supplierID,
			String name,
			String street,
			String city,
			String state,
			String zip,
			String phone,
			String url) {
		try {
			Util.debug("SuppliersBean.createSupplier() - Entered");
			Supplier supplier = null;
			supplier = em.find(Supplier.class, supplierID);
			if (supplier == null) {
				Util.debug("SuppliersBean.createSupplier() - supplier doesn't exist.");
				Util.debug("SuppliersBean.createSupplier() - Creating Supplier for SupplierID: " + supplierID);
				supplier = new Supplier(supplierID, name, street, city, state, zip, phone, url);
				em.persist(supplier);
			}
		} catch (Exception e) {
			Util.debug("SuppliersBean.createSupplier() - Exception: " + e);
		}
	}

	/**
	 * @return Supplier
	 */
	public Supplier getSupplier() {
		// Retrieve the first Supplier Info
		try {
			Collection<Supplier> suppliers = this.findSuppliers();
			if (suppliers != null) {
				Util.debug("AdminServlet.getSupplierInfo() - Supplier found!");
				Iterator<Supplier> i = suppliers.iterator();
				if (i.hasNext()) {
					return (Supplier) i.next();
				}
			}
		} catch (Exception e) {
			Util.debug("AdminServlet.getSupplierInfo() - Exception:" + e);
		}
		return null;
	}

	/**
	 * @param supplierID
	 * @param name
	 * @param street
	 * @param city
	 * @param state
	 * @param zip
	 * @param phone
	 * @param url
	 * @return supplierInfo
	 */
	public Supplier updateSupplier(String supplierID,
			String name,
			String street,
			String city,
			String state,
			String zip,
			String phone,
			String url) {
		Supplier supplier = null;
		try {
			Util.debug("SuppliersBean.updateSupplier() - Entered");
			supplier = em.find(Supplier.class, supplierID);
			if (supplier != null) {
				// Create a new Supplier if there is NOT an existing Supplier.
				// supplier = getSupplierLocalHome().findByPrimaryKey(new SupplierKey(supplierID));
				supplier.setName(name);
				supplier.setStreet(street);
				supplier.setCity(city);
				supplier.setUsstate(state);
				supplier.setZip(zip);
				supplier.setPhone(phone);
				supplier.setUrl(url);
			} else {
				Util.debug("SuppliersBean.updateSupplier() - supplier doesn't exist.");
				Util.debug("SuppliersBean.updateSupplier() - Couldn't update Supplier for SupplierID: " + supplierID);
			}
		} catch (Exception e) {
			Util.debug("SuppliersBean.createSupplier() - Exception: " + e);
		}
		return (supplier);
	}

	/**
	 * @return suppliers
	 */
	@SuppressWarnings("unchecked")
	private Collection<Supplier> findSuppliers() {
		Query q = em.createNamedQuery("findAllSuppliers");
		return q.getResultList();
	}
}
